from __future__ import annotations

import uuid

from django.db import models


class CustomerServiceImportBatch(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    shop_name = models.CharField(max_length=100)
    session_file_name = models.CharField(max_length=255)
    chat_file_name = models.CharField(max_length=255)
    raw_file_hash = models.CharField(max_length=64, db_index=True)
    import_hash = models.CharField(max_length=64, unique=True)
    content_hash = models.CharField(max_length=64, db_index=True)
    identity_set_hash = models.CharField(max_length=64)
    scope_key = models.CharField(max_length=64, db_index=True)
    published_state_token = models.CharField(max_length=64, default="")
    status = models.CharField(max_length=32)
    conversation_count = models.BigIntegerField(default=0)
    matched_count = models.BigIntegerField(default=0)
    session_only_count = models.BigIntegerField(default=0)
    chat_only_count = models.BigIntegerField(default=0)
    ambiguous_count = models.BigIntegerField(default=0)
    warnings_json = models.JSONField(default=dict)
    actor_email = models.CharField(max_length=320, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    migration_generation = models.CharField(max_length=64, default="", db_index=True)

    class Meta:
        db_table = "customer_service_import_batches"
        indexes = [
            models.Index(fields=["created_at", "id"], name="cs_batch_created_idx"),
            models.Index(fields=["shop_name", "completed_at"], name="cs_batch_shop_idx"),
        ]


class CustomerServiceConversation(models.Model):
    id = models.BigAutoField(primary_key=True)
    conversation_key = models.TextField(unique=True)
    first_import_batch_id = models.CharField(max_length=128)
    last_import_batch_id = models.CharField(max_length=128, db_index=True)
    shop_name = models.CharField(max_length=100, db_index=True)
    consulted_at = models.CharField(max_length=19, db_index=True)
    customer_id = models.TextField(default="")
    customer_alias = models.TextField(default="")
    consultation_type = models.TextField(default="")
    agent = models.CharField(max_length=200, default="", db_index=True)
    transferred_agent = models.TextField(default="")
    skill_group = models.TextField(default="")
    product_sku = models.CharField(max_length=200, default="", db_index=True)
    product_name = models.TextField(default="")
    first_response_at = models.CharField(max_length=19, default="")
    response_seconds = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    duration_minutes = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    customer_message_count = models.BigIntegerField(null=True, blank=True)
    agent_message_count = models.BigIntegerField(null=True, blank=True)
    satisfaction = models.TextField(default="")
    resolved = models.TextField(default="")
    conversation_id = models.TextField(default="")
    match_status = models.CharField(max_length=32, db_index=True)
    match_confidence = models.CharField(max_length=32)
    chat_started_at = models.CharField(max_length=19, default="")
    chat_ended_at = models.CharField(max_length=19, default="")
    chat_customer_alias = models.TextField(default="")
    messages = models.JSONField(default=list)
    robot_scope = models.CharField(max_length=32, default="", db_index=True)
    problem_type = models.CharField(max_length=32, default="", db_index=True)
    conversion_status = models.CharField(max_length=32, default="", db_index=True)
    service_issues = models.TextField(default="")
    summary_text = models.TextField(default="")
    analysis_source = models.CharField(max_length=16, default="")
    analyzed_at = models.DateTimeField(null=True, blank=True)
    annotated_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveBigIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    migration_generation = models.CharField(max_length=64, default="", db_index=True)

    class Meta:
        db_table = "customer_service_conversations"
        indexes = [
            models.Index(fields=["consulted_at", "id"], name="cs_conversation_date_idx"),
            models.Index(fields=["agent", "match_status", "consulted_at"], name="cs_conversation_filter_idx"),
            models.Index(fields=["shop_name", "last_import_batch_id"], name="cs_conversation_batch_idx"),
        ]


class CustomerServiceDeletionAudit(models.Model):
    audit_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation_id = models.BigIntegerField()
    conversation_key = models.TextField()
    actor = models.CharField(max_length=320)
    old_version = models.PositiveBigIntegerField()
    expected_version = models.PositiveBigIntegerField()
    reason = models.CharField(max_length=200)
    deleted_at = models.DateTimeField(auto_now_add=True)
    migration_generation = models.CharField(max_length=64, default="", db_index=True)

    class Meta:
        db_table = "customer_service_deletion_audits"


class CustomerServiceImportScopeHead(models.Model):
    scope_key = models.CharField(primary_key=True, max_length=64)
    shop_name = models.CharField(max_length=100, unique=True)
    state_token = models.CharField(max_length=64)
    status = models.CharField(max_length=16, default="ready")
    owner_token = models.CharField(max_length=64, default="")
    generation = models.BigIntegerField(default=0)
    current_batch_id = models.CharField(max_length=128, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "customer_service_import_scope_heads"


class CustomerServiceImportFingerprint(models.Model):
    id = models.BigAutoField(primary_key=True)
    domain = models.CharField(max_length=64, default="customer-service")
    batch_id = models.CharField(max_length=128)
    scope_key = models.CharField(max_length=64)
    scope_json = models.JSONField(default=dict)
    import_hash = models.CharField(max_length=64)
    raw_file_hash = models.CharField(max_length=64)
    content_hash = models.CharField(max_length=64)
    row_count = models.BigIntegerField()
    outcome = models.CharField(max_length=32)
    published_state_token = models.CharField(max_length=64, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    migration_generation = models.CharField(max_length=64, default="", db_index=True)

    class Meta:
        db_table = "customer_service_import_fingerprints"
        constraints = [
            models.UniqueConstraint(fields=["domain", "batch_id"], name="cs_fingerprint_batch_uq"),
            models.UniqueConstraint(fields=["domain", "scope_key", "import_hash"], name="cs_fingerprint_import_uq"),
        ]
        indexes = [models.Index(fields=["domain", "scope_key", "id"], name="cs_fingerprint_scope_idx")]


class CustomerServiceImportAttempt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    domain = models.CharField(max_length=64, default="customer-service")
    batch_id = models.CharField(max_length=128, default="")
    scope_key = models.CharField(max_length=64, default="")
    scope_json = models.JSONField(default=dict)
    import_hash = models.CharField(max_length=64, default="")
    raw_file_hash = models.CharField(max_length=64)
    content_hash = models.CharField(max_length=64, default="")
    row_count = models.BigIntegerField(default=0)
    file_name = models.CharField(max_length=520, default="")
    file_size_bytes = models.BigIntegerField(default=0)
    actor_email = models.CharField(max_length=320, default="")
    warnings_json = models.JSONField(default=dict)
    outcome = models.CharField(max_length=32)
    error_code = models.CharField(max_length=80, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    migration_generation = models.CharField(max_length=64, default="", db_index=True)

    class Meta:
        db_table = "customer_service_import_attempts"
        indexes = [
            models.Index(fields=["domain", "scope_key", "created_at"], name="cs_attempt_scope_idx"),
            models.Index(fields=["domain", "raw_file_hash", "created_at"], name="cs_attempt_raw_idx"),
        ]


class CustomerServiceDataRevision(models.Model):
    domain = models.CharField(primary_key=True, max_length=32)
    revision = models.BigIntegerField(default=0)
    source_digest = models.CharField(max_length=64, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "customer_service_data_revisions"


class CustomerServiceWriteAuthority(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    status = models.CharField(max_length=16, default="d1")
    authority_epoch = models.UUIDField(null=True, blank=True)
    cutover_id = models.CharField(max_length=128, default="")
    migration_verify_run_id = models.CharField(max_length=64, default="")
    activated_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "customer_service_write_authority"
        constraints = [
            models.CheckConstraint(condition=models.Q(id=1), name="cs_auth_singleton_ck"),
            models.CheckConstraint(condition=models.Q(status__in=["d1", "postgres"]), name="cs_auth_status_ck"),
        ]


class CustomerServiceWriteRequestReceipt(models.Model):
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
        db_table = "customer_service_write_request_receipts"


class CustomerServiceMigrationRun(models.Model):
    id = models.CharField(primary_key=True, max_length=64)
    mode = models.CharField(max_length=16)
    status = models.CharField(max_length=16)
    source_snapshot_digest = models.CharField(max_length=64)
    target_snapshot_digest = models.CharField(max_length=64, default="")
    source_counts = models.JSONField(default=dict)
    target_counts = models.JSONField(default=dict)
    manifest = models.JSONField(default=dict)
    approved_run_id = models.CharField(max_length=64, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "customer_service_migration_runs"


class CustomerServiceRawUploadSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fingerprint = models.CharField(max_length=255, db_index=True)
    kind = models.CharField(max_length=16)
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
        db_table = "customer_service_raw_upload_sessions"
        indexes = [
            models.Index(fields=["fingerprint", "expires_at"], name="cs_upload_fingerprint_idx"),
            models.Index(fields=["expires_at", "status"], name="cs_upload_expiry_idx"),
        ]


class CustomerServiceRawUploadChunk(models.Model):
    id = models.BigAutoField(primary_key=True)
    session = models.ForeignKey(CustomerServiceRawUploadSession, on_delete=models.CASCADE, related_name="chunks")
    chunk_index = models.PositiveIntegerField()
    size_bytes = models.BigIntegerField()
    sha256 = models.CharField(max_length=64)
    payload = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "customer_service_raw_upload_chunks"
        constraints = [models.UniqueConstraint(fields=["session", "chunk_index"], name="cs_upload_chunk_uq")]
        indexes = [models.Index(fields=["session", "chunk_index"], name="cs_upload_chunk_idx")]
