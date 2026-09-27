"""Prospective large-evidence physical ledger; no runtime collector imports it."""
from django.db import models
from django.utils import timezone


class AiBusinessV4Run(models.Model):
    id = models.CharField(primary_key=True, max_length=160)
    owner_email = models.CharField(max_length=320)
    scope_json = models.TextField(default="null")
    client_request_id = models.CharField(max_length=160)
    plan_json = models.TextField()
    plan_digest = models.CharField(max_length=64)
    run_identity_digest = models.CharField(max_length=64)
    status = models.CharField(max_length=20, default="collecting")
    collection_status = models.CharField(max_length=20, default="manual")
    version = models.PositiveBigIntegerField(default=1)
    stored_bytes = models.PositiveBigIntegerField(default=0)
    page_count = models.PositiveBigIntegerField(default=0)
    row_count = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_business_v4_runs"
        constraints = [models.UniqueConstraint(fields=["owner_email", "client_request_id"],
            name="ai_v4_run_client_uq")]
        indexes = [models.Index(fields=["owner_email", "-created_at"],
            name="ai_v4_run_owner_idx")]


class AiBusinessV4Source(models.Model):
    id = models.CharField(primary_key=True, max_length=160)
    run = models.ForeignKey(AiBusinessV4Run, on_delete=models.PROTECT)
    source_key = models.CharField(max_length=160)
    ordinal = models.PositiveIntegerField()
    domain = models.CharField(max_length=20)
    temporal_role = models.CharField(max_length=30)
    query_json = models.TextField()
    query_digest = models.CharField(max_length=64)
    source_identity_digest = models.CharField(max_length=64)
    source_revision_hint = models.CharField(max_length=128)
    source_ref = models.CharField(max_length=64, default="")
    source_revision = models.CharField(max_length=128, default="")
    checkpoint_json = models.TextField(default="{}")
    version = models.PositiveBigIntegerField(default=1)
    page_count = models.PositiveBigIntegerField(default=0)
    stored_bytes = models.PositiveBigIntegerField(default=0)
    row_count = models.PositiveBigIntegerField(default=0)
    finished = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_business_v4_sources"
        constraints = [
            models.UniqueConstraint(fields=["run", "source_key"], name="ai_v4_source_key_uq"),
            models.UniqueConstraint(fields=["run", "ordinal"], name="ai_v4_source_ord_uq"),
            models.UniqueConstraint(fields=["run", "domain", "query_digest"], name="ai_v4_source_query_uq"),
        ]
        indexes = [models.Index(fields=["run", "finished", "ordinal"], name="ai_v4_source_next_idx")]


class AiBusinessV4Chunk(models.Model):
    id = models.CharField(primary_key=True, max_length=160)
    run = models.ForeignKey(AiBusinessV4Run, on_delete=models.PROTECT)
    source = models.ForeignKey(AiBusinessV4Source, on_delete=models.PROTECT)
    sequence = models.PositiveBigIntegerField()
    payload_json = models.TextField()
    payload_digest = models.CharField(max_length=64)
    source_ref = models.CharField(max_length=64)
    source_revision = models.CharField(max_length=128)
    row_count = models.PositiveIntegerField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_business_v4_chunks"
        constraints = [models.UniqueConstraint(fields=["run", "source", "sequence"],
            name="ai_v4_chunk_sequence_uq")]


class AiBusinessV4ToolReceipt(models.Model):
    chunk = models.OneToOneField(AiBusinessV4Chunk, primary_key=True,
        on_delete=models.PROTECT, db_column="chunk_id")
    audit = models.OneToOneField("ai_assistant.AiToolAuditLogs", on_delete=models.PROTECT,
        db_column="audit_id")
    run = models.ForeignKey(AiBusinessV4Run, on_delete=models.PROTECT)
    source = models.ForeignKey(AiBusinessV4Source, on_delete=models.PROTECT)
    sequence = models.PositiveBigIntegerField()
    actor_email = models.CharField(max_length=320)
    request_id = models.CharField(max_length=128)
    invocation_id = models.CharField(max_length=160)
    tool_name = models.CharField(max_length=100)
    surface = models.CharField(max_length=40, default="business_collection")
    response_digest = models.CharField(max_length=64)
    payload_bytes = models.PositiveIntegerField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_business_v4_tool_receipts"
        constraints = [models.UniqueConstraint(fields=["run", "source", "sequence"],
            name="ai_v4_receipt_seq_uq")]
