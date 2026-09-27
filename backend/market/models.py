from __future__ import annotations

import uuid

from django.db import models


class MarketImportBatch(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    source_type = models.CharField(max_length=64)
    file_name = models.TextField()
    file_size_bytes = models.BigIntegerField()
    raw_file_hash = models.CharField(max_length=64, db_index=True)
    content_hash = models.CharField(max_length=64, db_index=True)
    sheet_name = models.TextField(default="")
    status = models.CharField(max_length=32)
    row_count = models.BigIntegerField(default=0)
    inserted_count = models.BigIntegerField(default=0)
    updated_count = models.BigIntegerField(default=0)
    warning_count = models.BigIntegerField(default=0)
    period_start = models.CharField(max_length=10, null=True, blank=True)
    period_end = models.CharField(max_length=10, null=True, blank=True)
    warnings_json = models.JSONField(default=list)
    scope_json = models.JSONField(default=dict)
    published_state_token = models.CharField(max_length=64, default="")
    actor_email = models.CharField(max_length=320, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    migration_generation = models.CharField(max_length=64, default="", db_index=True)

    class Meta:
        db_table = "market_import_batches"
        indexes = [
            models.Index(fields=["created_at"], name="mkt_batch_created_idx"),
            models.Index(fields=["status", "completed_at"], name="mkt_batch_status_idx"),
        ]


class MarketRankingEntry(models.Model):
    id = models.BigAutoField(primary_key=True)
    natural_key = models.TextField(unique=True)
    source_row_number = models.BigIntegerField()
    period_start = models.CharField(max_length=10)
    period_end = models.CharField(max_length=10)
    category = models.TextField(default="")
    scope = models.TextField(default="全部")
    price_band_filter = models.TextField(default="全部")
    ranking_dimension = models.CharField(max_length=8, default="SKU")
    operation_mode = models.CharField(max_length=16, default="未知")
    subcategory = models.TextField(default="")
    source_brand = models.TextField(default="")
    source_operation_mode = models.TextField(default="")
    source_subcategory = models.TextField(default="")
    rank = models.BigIntegerField(null=True, blank=True)
    sku_code = models.TextField()
    product_name = models.TextField(default="")
    brand = models.TextField(default="")
    price_cents = models.BigIntegerField(null=True, blank=True)
    price_low_cents = models.BigIntegerField(null=True, blank=True)
    price_high_cents = models.BigIntegerField(null=True, blank=True)
    price_estimated = models.BooleanField(default=False)
    price_raw = models.TextField(default="")
    gmv_cents = models.BigIntegerField(default=0)
    gmv_low_cents = models.BigIntegerField(null=True, blank=True)
    gmv_high_cents = models.BigIntegerField(null=True, blank=True)
    gmv_raw = models.TextField(default="")
    quantity = models.BigIntegerField(default=0)
    quantity_low = models.BigIntegerField(null=True, blank=True)
    quantity_high = models.BigIntegerField(null=True, blank=True)
    quantity_raw = models.TextField(default="")
    page_views = models.BigIntegerField(default=0)
    page_views_raw = models.TextField(default="")
    visitors = models.BigIntegerField(default=0)
    visitors_low = models.BigIntegerField(null=True, blank=True)
    visitors_high = models.BigIntegerField(null=True, blank=True)
    visitors_raw = models.TextField(default="")
    conversion_bps = models.BigIntegerField(null=True, blank=True)
    conversion_low_bps = models.BigIntegerField(null=True, blank=True)
    conversion_high_bps = models.BigIntegerField(null=True, blank=True)
    conversion_raw = models.TextField(default="")
    cart_customers = models.BigIntegerField(default=0)
    cart_customers_raw = models.TextField(default="")
    search_clicks = models.BigIntegerField(default=0)
    search_clicks_raw = models.TextField(default="")
    image_url = models.TextField(default="")
    product_url = models.TextField(default="")
    raw_json = models.JSONField(default=dict)
    last_import_batch_id = models.CharField(max_length=128, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    migration_generation = models.CharField(max_length=64, default="", db_index=True)

    class Meta:
        db_table = "market_ranking_entries"
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "period_start",
                    "period_end",
                    "category",
                    "scope",
                    "price_band_filter",
                    "ranking_dimension",
                    "sku_code",
                ],
                name="mkt_entry_business_uq",
            )
        ]
        indexes = [
            models.Index(fields=["period_end", "period_start"], name="mkt_entry_period_idx"),
            models.Index(fields=["category", "period_end"], name="mkt_entry_category_idx"),
            models.Index(fields=["sku_code", "period_end"], name="mkt_entry_sku_idx"),
            models.Index(fields=["brand", "period_end"], name="mkt_entry_brand_idx"),
            models.Index(fields=["scope"], name="mkt_facet_scope_idx"),
            models.Index(fields=["ranking_dimension"], name="mkt_facet_dimension_idx"),
            models.Index(fields=["operation_mode"], name="mkt_facet_operation_idx"),
            models.Index(fields=["subcategory"], name="mkt_facet_subcategory_idx"),
            models.Index(
                fields=["category", "scope", "ranking_dimension", "sku_code", "-period_end", "-id"],
                name="mkt_entry_identity_idx",
            ),
        ]


class MarketMasterIdentity(models.Model):
    id = models.BigAutoField(primary_key=True)
    category = models.TextField()
    scope = models.TextField()
    ranking_dimension = models.CharField(max_length=8)
    sku_code = models.TextField()
    latest_entry_id = models.BigIntegerField(unique=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "market_master_identities"
        constraints = [
            models.UniqueConstraint(
                fields=["category", "scope", "ranking_dimension", "sku_code"],
                name="mkt_master_identity_uq",
            )
        ]


class MarketSkuGmvTotal(models.Model):
    sku_code = models.TextField(primary_key=True)
    gmv_total_cents = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "market_sku_gmv_totals"


class MarketPriceSnapshot(models.Model):
    id = models.CharField(primary_key=True, max_length=512)
    category = models.TextField()
    scope = models.TextField(default="")
    sku_code = models.TextField()
    ranking_dimension = models.CharField(max_length=8, default="SKU")
    month = models.CharField(max_length=7)
    source_price_cents = models.BigIntegerField(null=True, blank=True)
    ai_image_price_cents = models.BigIntegerField(null=True, blank=True)
    ai_price_type = models.CharField(max_length=32, default="")
    ai_confidence_bps = models.BigIntegerField(null=True, blank=True)
    ai_reason = models.TextField(default="")
    confirmed_market_price_cents = models.BigIntegerField(null=True, blank=True)
    average_transaction_price_cents = models.BigIntegerField(null=True, blank=True)
    price_low_cents = models.BigIntegerField(null=True, blank=True)
    price_high_cents = models.BigIntegerField(null=True, blank=True)
    image_content_sha256 = models.CharField(max_length=64, default="", db_index=True)
    image_url = models.TextField(default="")
    confirmation_status = models.CharField(max_length=32, default="source_table")
    confirmed_by = models.CharField(max_length=320, default="")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    source_job_item_id = models.CharField(max_length=128, default="")
    prompt_version_id = models.CharField(max_length=128, default="")
    source_import_batch_id = models.CharField(max_length=128, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    migration_generation = models.CharField(max_length=64, default="", db_index=True)

    class Meta:
        db_table = "market_price_snapshots"
        constraints = [
            models.UniqueConstraint(
                fields=["category", "scope", "sku_code", "ranking_dimension", "month"],
                name="mkt_price_snapshot_uq",
            )
        ]
        indexes = [
            models.Index(fields=["confirmation_status", "updated_at"], name="mkt_price_status_idx"),
            models.Index(fields=["sku_code", "image_content_sha256", "confirmed_at"], name="mkt_price_reuse_idx"),
        ]


class MarketDataRevision(models.Model):
    domain = models.CharField(primary_key=True, max_length=32)
    revision = models.BigIntegerField(default=0)
    source_digest = models.CharField(max_length=64, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "market_data_revisions"


class MarketImportScopeHead(models.Model):
    scope_key = models.CharField(primary_key=True, max_length=64)
    state_token = models.CharField(max_length=64, default="initial")
    status = models.CharField(max_length=32, default="ready")
    owner_token = models.CharField(max_length=64, default="")
    current_batch_id = models.CharField(max_length=128, default="")
    generation = models.BigIntegerField(default=0)
    owner_started_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "market_import_scope_heads"


class MarketImportAttempt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch_id = models.CharField(max_length=128, default="")
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
        db_table = "market_import_attempts"
        indexes = [models.Index(fields=["scope_key", "created_at"], name="mkt_attempt_scope_idx")]


class MarketImportFingerprint(models.Model):
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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "market_import_fingerprints"


class MarketWriteAuthority(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    status = models.CharField(max_length=16, default="d1")
    authority_epoch = models.UUIDField(null=True, blank=True)
    cutover_id = models.CharField(max_length=128, default="")
    migration_verify_run_id = models.CharField(max_length=64, default="")
    activated_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "market_write_authority"
        constraints = [
            models.CheckConstraint(condition=models.Q(id=1), name="mkt_auth_singleton_ck"),
            models.CheckConstraint(
                condition=models.Q(status__in=["d1", "postgres"]),
                name="mkt_auth_status_ck",
            ),
        ]


class MarketWriteRequestReceipt(models.Model):
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
        db_table = "market_write_request_receipts"


class MarketMigrationRun(models.Model):
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
        db_table = "market_migration_runs"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(completed_at__isnull=True)
                | models.Q(completed_at__gte=models.F("created_at")),
                name="mkt_migration_time_ck",
            )
        ]


class MarketImageCache(models.Model):
    source_url = models.TextField(primary_key=True)
    status = models.CharField(max_length=32, default="pending")
    object_key = models.TextField(default="")
    content_sha256 = models.CharField(max_length=64, default="", db_index=True)
    mime_type = models.CharField(max_length=100, default="")
    size_bytes = models.BigIntegerField(default=0)
    image_source = models.CharField(max_length=32, default="")
    attempt_count = models.BigIntegerField(default=0)
    error_code = models.CharField(max_length=64, default="")
    error_message = models.TextField(default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    migration_generation = models.CharField(max_length=64, default="", db_index=True)

    class Meta:
        db_table = "market_image_cache"
        indexes = [
            models.Index(fields=["status", "updated_at"], name="mkt_image_status_idx"),
            models.Index(fields=["object_key"], name="mkt_image_object_idx"),
        ]


class MarketImageCacheJob(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    scope_key = models.TextField(unique=True)
    batch_id = models.CharField(max_length=128, default="")
    status = models.CharField(max_length=32, default="queued")
    requested_by = models.CharField(max_length=320, default="")
    discovery_cursor = models.TextField(default="")
    discovery_complete = models.BooleanField(default=False)
    discovered_count = models.BigIntegerField(default=0)
    completed_count = models.BigIntegerField(default=0)
    failed_count = models.BigIntegerField(default=0)
    pending_count = models.BigIntegerField(default=0)
    propagation_pending_count = models.BigIntegerField(default=0)
    processed_count = models.BigIntegerField(default=0)
    run_count = models.BigIntegerField(default=0)
    failure_count = models.BigIntegerField(default=0)
    lease_token_hash = models.CharField(max_length=64, default="")
    lease_epoch = models.BigIntegerField(default=0)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=64, default="")
    error_message = models.TextField(default="")
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "market_image_cache_jobs"
        indexes = [
            models.Index(fields=["status", "next_run_at", "lease_expires_at"], name="mkt_image_job_run_idx"),
            models.Index(fields=["batch_id", "updated_at"], name="mkt_image_job_batch_idx"),
        ]


class MarketImageCacheJobItem(models.Model):
    id = models.BigAutoField(primary_key=True)
    job_id = models.CharField(max_length=128)
    source_url = models.TextField()
    status = models.CharField(max_length=32, default="queued")
    content_sha256 = models.CharField(max_length=64, default="")
    attempt_count = models.BigIntegerField(default=0)
    error_code = models.CharField(max_length=64, default="")
    error_message = models.TextField(default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "market_image_cache_job_items"
        constraints = [
            models.UniqueConstraint(fields=["job_id", "source_url"], name="mkt_image_job_item_uq")
        ]
        indexes = [
            models.Index(fields=["job_id", "status"], name="mkt_image_item_work_idx")
        ]


class MarketImageCacheClaim(models.Model):
    source_url = models.TextField(primary_key=True)
    job_id = models.CharField(max_length=128)
    claim_token_hash = models.CharField(max_length=64)
    job_lease_token_hash = models.CharField(max_length=64)
    job_epoch = models.BigIntegerField()
    attempt_count = models.BigIntegerField()
    lease_expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "market_image_cache_claims"
        indexes = [
            models.Index(fields=["job_id", "lease_expires_at"], name="mkt_image_claim_idx")
        ]


class MarketPriceBandVersion(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    category = models.TextField(default="*")
    version = models.BigIntegerField()
    status = models.CharField(max_length=32, default="draft")
    effective_from = models.CharField(max_length=10, default="1970-01-01")
    created_by = models.CharField(max_length=320, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    published_by = models.CharField(max_length=320, default="")
    published_at = models.DateTimeField(null=True, blank=True)
    rolled_back_from_id = models.CharField(max_length=128, default="")
    note = models.TextField(default="")

    class Meta:
        db_table = "market_price_band_versions"
        constraints = [
            models.UniqueConstraint(fields=["category", "version"], name="mkt_price_band_version_uq"),
            models.UniqueConstraint(
                fields=["category"],
                condition=models.Q(status="published"),
                name="mkt_price_band_live_uq",
            ),
        ]


class MarketPriceBandItem(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    version_id = models.CharField(max_length=128, db_index=True)
    label = models.TextField()
    min_cents = models.BigIntegerField(null=True, blank=True)
    max_cents = models.BigIntegerField(null=True, blank=True)
    sort_order = models.BigIntegerField(default=0)

    class Meta:
        db_table = "market_price_band_items"


class MarketMasterMappingRule(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    kind = models.CharField(max_length=32)
    category = models.TextField(default="")
    source_value = models.TextField()
    target_value = models.TextField()
    status = models.CharField(max_length=32, default="draft")
    version = models.BigIntegerField(default=1)
    effective_from = models.CharField(max_length=10, default="1970-01-01")
    created_by = models.CharField(max_length=320, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "market_master_mapping_rules"
        indexes = [
            models.Index(fields=["kind", "status", "effective_from"], name="mkt_mapping_lookup_idx")
        ]


class MarketSubcategoryTaxonomy(models.Model):
    id = models.CharField(primary_key=True, max_length=512)
    category = models.TextField()
    subcategory = models.TextField()
    status = models.CharField(max_length=32, default="active")
    sort_order = models.BigIntegerField(default=0)
    created_by = models.CharField(max_length=320, default="")
    updated_by = models.CharField(max_length=320, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "market_subcategory_taxonomy"
        constraints = [
            models.UniqueConstraint(fields=["category", "subcategory"], name="mkt_taxonomy_value_uq")
        ]
        indexes = [
            models.Index(fields=["category", "status", "sort_order"], name="mkt_taxonomy_lookup_idx")
        ]


class MarketBrandSuggestion(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    category = models.TextField()
    scope = models.TextField()
    ranking_dimension = models.CharField(max_length=8, default="SKU")
    sku_code = models.TextField()
    product_name = models.TextField(default="")
    current_brand = models.TextField(default="")
    ai_brand = models.TextField(default="")
    status = models.CharField(max_length=32, default="ai_pending")
    model_id = models.CharField(max_length=128, default="")
    error_message = models.TextField(default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    confirmed_by = models.CharField(max_length=320, default="")
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "market_brand_suggestions"
        constraints = [
            models.UniqueConstraint(
                fields=["category", "scope", "ranking_dimension", "sku_code"],
                name="mkt_brand_suggestion_uq",
            )
        ]


class MarketBrandRecognitionJob(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    model_id = models.CharField(max_length=128)
    query_text = models.TextField(default="")
    category = models.TextField(default="")
    status = models.CharField(max_length=32, default="queued")
    total_count = models.BigIntegerField(default=0)
    processed_count = models.BigIntegerField(default=0)
    recognized_count = models.BigIntegerField(default=0)
    empty_count = models.BigIntegerField(default=0)
    batch_size = models.BigIntegerField(default=40)
    created_by = models.CharField(max_length=320, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(default="")
    lease_token_hash = models.CharField(max_length=64, default="")
    lease_expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "market_brand_recognition_jobs"


class MarketBrandSeed(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    canonical_brand = models.TextField()
    seed_text = models.TextField()
    normalized_seed = models.TextField(unique=True)
    source = models.CharField(max_length=32, default="manual")
    source_ref = models.TextField(default="")
    status = models.CharField(max_length=32, default="enabled")
    created_by = models.CharField(max_length=320, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_refreshed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "market_brand_seeds"
        indexes = [
            models.Index(fields=["status", "canonical_brand", "source"], name="mkt_brand_seed_idx")
        ]


class MarketDownloadConfig(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    category = models.TextField()
    scope = models.TextField(default="全部")
    ranking_dimension = models.CharField(max_length=8)
    month_start = models.CharField(max_length=7)
    month_end = models.CharField(max_length=7)
    status = models.CharField(max_length=32, default="enabled")
    created_by = models.CharField(max_length=320, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "market_download_configs"
        constraints = [
            models.UniqueConstraint(
                fields=["category", "scope", "ranking_dimension", "month_start", "month_end"],
                name="mkt_download_config_uq",
            )
        ]


class MarketDownloadTask(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    category = models.TextField()
    scope = models.TextField(default="全部")
    month = models.CharField(max_length=7)
    ranking_dimension = models.CharField(max_length=8)
    status = models.CharField(max_length=32, default="planned")
    attempt_count = models.BigIntegerField(default=0)
    jd_task_id = models.TextField(default="")
    source_file_name = models.TextField(default="")
    file_hash = models.CharField(max_length=64, default="")
    row_count = models.BigIntegerField(default=0)
    header_valid = models.BooleanField(default=False)
    period_valid = models.BooleanField(default=False)
    category_valid = models.BooleanField(default=False)
    dimension_valid = models.BooleanField(default=False)
    import_batch_id = models.CharField(max_length=128, default="")
    validation_json = models.JSONField(default=dict)
    execution_token_hash = models.CharField(max_length=64, default="")
    error_code = models.CharField(max_length=64, default="")
    error_message = models.TextField(default="")
    next_retry_at = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "market_download_tasks"
        constraints = [
            models.UniqueConstraint(
                fields=["category", "scope", "month", "ranking_dimension"],
                name="mkt_download_task_uq",
            )
        ]


class MarketMasterAuditLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor_email = models.CharField(max_length=320)
    actor_role = models.CharField(max_length=32)
    action = models.CharField(max_length=64)
    entity_type = models.CharField(max_length=64)
    entity_id = models.TextField()
    before_json = models.JSONField(default=dict)
    after_json = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "market_master_audit_logs"
        indexes = [
            models.Index(fields=["entity_type", "entity_id", "created_at"], name="mkt_audit_entity_idx")
        ]


class MarketAnnotationPromptVersion(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    category = models.TextField()
    version = models.BigIntegerField()
    parent_id = models.CharField(max_length=128, null=True, blank=True)
    source = models.CharField(max_length=32)
    status = models.CharField(max_length=32, default="draft")
    segments_json = models.JSONField(default=list)
    prompt_body = models.TextField()
    change_note = models.TextField(default="")
    metrics_json = models.JSONField(default=dict)
    created_by = models.CharField(max_length=320)
    created_at = models.DateTimeField(auto_now_add=True)
    activated_by = models.CharField(max_length=320, null=True, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "market_annotation_prompt_versions"
        constraints = [
            models.UniqueConstraint(fields=["category", "version"], name="mkt_prompt_version_uq"),
            models.UniqueConstraint(
                fields=["category"],
                condition=models.Q(status="active"),
                name="mkt_prompt_active_uq",
            ),
        ]


class MarketAnnotationJob(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    category = models.TextField()
    prompt_version_id = models.CharField(max_length=128)
    executor = models.CharField(max_length=16)
    model_id = models.CharField(max_length=128, null=True, blank=True)
    local_model_name = models.TextField(default="")
    work_key = models.TextField(default="")
    reuse_status = models.CharField(max_length=32, default="pending")
    reuse_started_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=32, default="queued")
    total_count = models.BigIntegerField(default=0)
    completed_count = models.BigIntegerField(default=0)
    failed_count = models.BigIntegerField(default=0)
    reviewed_count = models.BigIntegerField(default=0)
    committed_count = models.BigIntegerField(default=0)
    created_by = models.CharField(max_length=320)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    commit_token_hash = models.CharField(max_length=64, default="")
    commit_started_at = models.DateTimeField(null=True, blank=True)
    migration_generation = models.CharField(max_length=64, default="", db_index=True)

    class Meta:
        db_table = "market_annotation_jobs"
        constraints = [
            models.UniqueConstraint(
                fields=["work_key"],
                condition=~models.Q(work_key="")
                & models.Q(status__in=["queued", "running", "failed"]),
                name="mkt_annotation_work_uq",
            )
        ]
        indexes = [
            models.Index(fields=["category", "created_at"], name="mkt_annotation_job_idx")
        ]


class MarketAnnotationItem(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    job_id = models.CharField(max_length=128, db_index=True)
    category = models.TextField(default="")
    scope = models.TextField(default="")
    sku_code = models.TextField()
    ranking_dimension = models.CharField(max_length=8, default="SKU")
    month = models.CharField(max_length=7, default="")
    image_content_sha256 = models.CharField(max_length=64, default="")
    product_name = models.TextField(default="")
    brand = models.TextField(default="")
    source_image_url = models.TextField(default="")
    resolved_image_url = models.TextField(default="")
    image_source = models.CharField(max_length=32, default="none")
    status = models.CharField(max_length=32, default="queued")
    ai_segment = models.TextField(default="")
    ai_image_price_cents = models.BigIntegerField(null=True, blank=True)
    ai_price_type = models.CharField(max_length=32, default="")
    ai_price_low_cents = models.BigIntegerField(null=True, blank=True)
    ai_price_high_cents = models.BigIntegerField(null=True, blank=True)
    ai_confidence_bps = models.BigIntegerField(null=True, blank=True)
    ai_reason = models.TextField(default="")
    ai_raw_digest = models.CharField(max_length=64, default="")
    model_input_bytes = models.BigIntegerField(default=0)
    image_load_ms = models.BigIntegerField(default=0)
    image_prepare_ms = models.BigIntegerField(default=0)
    model_call_ms = models.BigIntegerField(default=0)
    total_inference_ms = models.BigIntegerField(default=0)
    reviewed_segment = models.TextField(default="")
    reviewed_image_price_cents = models.BigIntegerField(null=True, blank=True)
    reviewed_price_type = models.CharField(max_length=32, default="")
    reviewed_price_low_cents = models.BigIntegerField(null=True, blank=True)
    reviewed_price_high_cents = models.BigIntegerField(null=True, blank=True)
    selected = models.BooleanField(default=False)
    reviewed_by = models.CharField(max_length=320, default="")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    lease_token_hash = models.CharField(max_length=64, default="")
    lease_agent_id = models.CharField(max_length=128, default="")
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    attempt_count = models.BigIntegerField(default=0)
    error_message = models.TextField(default="")
    version = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    migration_generation = models.CharField(max_length=64, default="", db_index=True)

    class Meta:
        db_table = "market_annotation_items"
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "job_id",
                    "category",
                    "scope",
                    "sku_code",
                    "ranking_dimension",
                    "month",
                    "image_content_sha256",
                ],
                name="mkt_annotation_snapshot_uq",
            )
        ]
        indexes = [
            models.Index(fields=["job_id", "status", "updated_at"], name="mkt_annotation_status_idx"),
            models.Index(fields=["lease_expires_at", "status"], name="mkt_annotation_lease_idx"),
            models.Index(
                fields=["category", "scope", "sku_code", "ranking_dimension", "image_content_sha256"],
                name="mkt_annotation_reuse_idx",
            ),
        ]


class MarketSkuAnnotation(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    category = models.TextField()
    scope = models.TextField(default="全部")
    sku_code = models.TextField()
    ranking_dimension = models.CharField(max_length=8, default="SKU")
    image_content_sha256 = models.CharField(max_length=64, default="")
    segment = models.TextField()
    image_price_cents = models.BigIntegerField(null=True, blank=True)
    image_url = models.TextField(default="")
    image_source = models.CharField(max_length=32, default="none")
    confidence_bps = models.BigIntegerField(null=True, blank=True)
    source_job_item_id = models.CharField(max_length=128)
    prompt_version_id = models.CharField(max_length=128)
    reviewed_by = models.CharField(max_length=320)
    reviewed_at = models.DateTimeField()
    version = models.BigIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "market_sku_annotations"
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "category",
                    "scope",
                    "ranking_dimension",
                    "sku_code",
                    "image_content_sha256",
                ],
                name="mkt_sku_annotation_uq",
            )
        ]
        indexes = [
            models.Index(
                fields=["category", "scope", "ranking_dimension", "segment", "updated_at"],
                name="mkt_sku_annotation_idx",
            )
        ]


class MarketAnnotationCommitReceipt(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    job_item_id = models.CharField(max_length=128, unique=True)
    annotation_id = models.CharField(max_length=128)
    idempotency_key = models.CharField(max_length=128, unique=True)
    before_json = models.JSONField(default=dict)
    after_json = models.JSONField()
    committed_by = models.CharField(max_length=320)
    committed_at = models.DateTimeField(auto_now_add=True)
    batch_id = models.CharField(max_length=128, default="", db_index=True)
    request_digest = models.CharField(max_length=64, default="")

    class Meta:
        db_table = "market_annotation_commit_receipts"


class MarketAnnotationValidationSample(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    category = models.TextField()
    scope = models.TextField(default="全部")
    sku_code = models.TextField()
    ranking_dimension = models.CharField(max_length=8, default="SKU")
    image_content_sha256 = models.CharField(max_length=64, default="")
    product_name = models.TextField(default="")
    brand = models.TextField(default="")
    image_url = models.TextField(default="")
    gold_segment = models.TextField()
    gold_image_price_cents = models.BigIntegerField(null=True, blank=True)
    source_annotation_id = models.CharField(max_length=128, default="")
    created_by = models.CharField(max_length=320)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "market_annotation_validation_samples"
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "category",
                    "scope",
                    "ranking_dimension",
                    "sku_code",
                    "image_content_sha256",
                ],
                name="mkt_validation_sample_uq",
            )
        ]


class MarketAnnotationValidationRun(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    category = models.TextField()
    baseline_prompt_id = models.CharField(max_length=128, null=True, blank=True)
    candidate_prompt_id = models.CharField(max_length=128)
    model_id = models.CharField(max_length=128)
    status = models.CharField(max_length=32, default="queued")
    seed = models.CharField(max_length=128)
    requested_sample_count = models.BigIntegerField(default=50)
    sample_count = models.BigIntegerField(default=0)
    sample_hash = models.CharField(max_length=64, default="")
    metrics_json = models.JSONField(default=dict)
    gate_json = models.JSONField(default=dict)
    created_by = models.CharField(max_length=320)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "market_annotation_validation_runs"
        indexes = [
            models.Index(fields=["candidate_prompt_id", "created_at"], name="mkt_validation_run_idx")
        ]


class MarketAnnotationValidationResult(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    run_id = models.CharField(max_length=128)
    sample_id = models.CharField(max_length=128)
    prompt_version_id = models.CharField(max_length=128)
    status = models.CharField(max_length=32, default="queued")
    predicted_segment = models.TextField(default="")
    predicted_image_price_cents = models.BigIntegerField(null=True, blank=True)
    confidence_bps = models.BigIntegerField(null=True, blank=True)
    is_correct = models.BooleanField(default=False)
    error_message = models.TextField(default="")
    created_at = models.DateTimeField(auto_now_add=True)
    sample_snapshot_json = models.JSONField(default=dict)
    claim_token_hash = models.CharField(max_length=64, default="")
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    attempt_count = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "market_annotation_validation_results"
        constraints = [
            models.UniqueConstraint(
                fields=["run_id", "sample_id", "prompt_version_id"],
                name="mkt_validation_result_uq",
            )
        ]
        indexes = [
            models.Index(fields=["run_id", "status", "lease_expires_at"], name="mkt_validation_lease_idx")
        ]


class MarketAnnotationPromptAudit(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    prompt_id = models.CharField(max_length=128)
    category = models.TextField()
    action = models.CharField(max_length=64)
    reason = models.TextField()
    actor = models.CharField(max_length=320)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "market_annotation_prompt_audits"
        indexes = [models.Index(fields=["prompt_id", "created_at"], name="mkt_prompt_audit_idx")]


class MarketAnnotationLocalAgent(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    name = models.TextField()
    token_hash = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=32, default="enabled")
    capabilities_json = models.JSONField(default=dict)
    created_by = models.CharField(max_length=320)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "market_annotation_local_agents"


class MarketAnnotationConcurrencySetting(models.Model):
    id = models.BigAutoField(primary_key=True)
    category = models.TextField()
    executor = models.CharField(max_length=16)
    concurrency = models.PositiveSmallIntegerField()
    updated_by = models.CharField(max_length=320)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "market_annotation_concurrency_settings"
        constraints = [
            models.UniqueConstraint(fields=["category", "executor"], name="mkt_annotation_concurrency_uq"),
            models.CheckConstraint(
                condition=models.Q(concurrency__gte=1, concurrency__lte=50),
                name="mkt_annotation_concurrency_ck",
            ),
        ]


class MarketAnnotationCloudRun(models.Model):
    job_id = models.CharField(primary_key=True, max_length=128)
    state = models.CharField(max_length=16, default="running")
    retry_state_json = models.JSONField(default=dict)
    next_run_at = models.DateTimeField(null=True, blank=True)
    lease_token_hash = models.CharField(max_length=64, default="")
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    last_failure_code = models.CharField(max_length=64, default="")
    last_failure_message = models.TextField(default="")
    last_started_at = models.DateTimeField(null=True, blank=True)
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "market_annotation_cloud_runs"
        indexes = [
            models.Index(fields=["state", "next_run_at", "lease_expires_at"], name="mkt_cloud_run_ready_idx")
        ]


class MarketNetshopProjection(models.Model):
    id = models.BigAutoField(primary_key=True)
    projection_revision = models.CharField(max_length=96)
    projection_key = models.TextField()
    kind = models.CharField(max_length=16)
    source = models.CharField(max_length=64, default="")
    dataset = models.CharField(max_length=64, default="")
    platform = models.CharField(max_length=100, default="")
    shop_name = models.CharField(max_length=100, default="")
    business_date = models.CharField(max_length=10, default="")
    sku_id = models.TextField(default="")
    spu_id = models.TextField(default="")
    product_code = models.TextField(default="")
    transaction_amount_cents = models.BigIntegerField(default=0)
    brand = models.TextField(default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "market_netshop_projection"
        constraints = [
            models.UniqueConstraint(
                fields=["projection_revision", "projection_key"],
                name="mkt_netshop_projection_uq",
            )
        ]
        indexes = [
            models.Index(
                fields=["projection_revision", "kind", "business_date"],
                name="mkt_netshop_metric_idx",
            ),
            models.Index(
                fields=["projection_revision", "kind", "sku_id", "spu_id"],
                name="mkt_netshop_identity_idx",
            ),
        ]


class MarketNetshopProjectionControl(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    active_revision = models.CharField(max_length=96, default="")
    active_total = models.BigIntegerField(default=0)
    syncing_revision = models.CharField(max_length=96, default="")
    syncing_total = models.BigIntegerField(default=0)
    syncing_offset = models.BigIntegerField(default=0)
    syncing_owner = models.CharField(max_length=320, default="")
    owner_token_hash = models.CharField(max_length=64, default="")
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "market_netshop_projection_control"
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name="mkt_netshop_control_ck")]

class MarketAnalysisOption(models.Model):
    """Current historical publication directory; replaced atomically on rebuild."""
    id = models.BigAutoField(primary_key=True)
    category = models.CharField(max_length=200)
    scope = models.CharField(max_length=200)
    ranking_dimension = models.CharField(max_length=3)
    price_band_filter = models.CharField(max_length=200)
    first_date = models.CharField(max_length=10)
    last_date = models.CharField(max_length=10)
    entry_json = models.TextField()
    entry_digest = models.CharField(max_length=64)
    search_casefold = models.CharField(max_length=2000)

    class Meta:
        db_table = "market_analysis_options"
        constraints = [models.UniqueConstraint(fields=["category", "scope", "ranking_dimension", "price_band_filter"],
                                               name="mkt_options_identity_uq")]


class MarketAnalysisOptionsState(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    status = models.CharField(max_length=16, default="not_ready")
    generation = models.CharField(max_length=64, default="")
    directory_digest = models.CharField(max_length=64, default="")
    identity_count = models.PositiveIntegerField(default=0)
    stored_bytes = models.PositiveIntegerField(default=0)
    source_revision = models.CharField(max_length=160, default="")
    reason = models.CharField(max_length=64, default="not_initialized")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "market_analysis_options_state"
