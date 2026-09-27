from django.db import models
from django.utils import timezone


class AiBusinessEvidenceRun(models.Model):
    id = models.CharField(primary_key=True, max_length=160)
    owner_email = models.CharField(max_length=320)
    scope_json = models.TextField(default="null")
    client_request_id = models.CharField(max_length=160)
    request_digest = models.CharField(max_length=64)
    plan_json = models.TextField()
    state_json = models.TextField(default="{}")
    status = models.CharField(max_length=20, default="collecting")
    version = models.PositiveIntegerField(default=1)
    stored_bytes = models.PositiveBigIntegerField(default=0)
    collection_status = models.CharField(max_length=20, default="manual")
    next_collect_at = models.DateTimeField(default=timezone.now)
    collection_failures = models.PositiveIntegerField(default=0)
    collection_error_code = models.CharField(max_length=64, default="")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_business_evidence_runs"
        constraints = [models.UniqueConstraint(fields=["owner_email", "client_request_id"], name="ai_business_client_uq")]
        indexes = [models.Index(fields=["owner_email", "-created_at"], name="ai_business_owner_idx")]


class AiBusinessEvidenceChunk(models.Model):
    id = models.CharField(primary_key=True, max_length=160)
    run = models.ForeignKey(AiBusinessEvidenceRun, on_delete=models.PROTECT)
    source_key = models.CharField(max_length=160)
    sequence = models.PositiveIntegerField()
    payload_json = models.TextField()
    payload_digest = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_business_evidence_chunks"
        constraints = [models.UniqueConstraint(fields=["run", "source_key", "sequence"], name="ai_business_chunk_uq")]


class AiBusinessEvidenceSource(models.Model):
    """Candidate v2 directory; existing v1 run/chunk bytes are unchanged."""
    id = models.CharField(primary_key=True, max_length=160)
    run = models.ForeignKey(AiBusinessEvidenceRun, on_delete=models.PROTECT)
    source_key = models.CharField(max_length=160)
    ordinal = models.PositiveIntegerField()
    domain = models.CharField(max_length=20)
    query_json = models.TextField()
    query_digest = models.CharField(max_length=64)
    checkpoint_json = models.TextField(default="{}")
    version = models.PositiveIntegerField(default=1)
    checkpoint_run_version = models.PositiveIntegerField(default=1)
    page_count = models.PositiveIntegerField(default=0)
    stored_bytes = models.PositiveBigIntegerField(default=0)
    row_count = models.PositiveBigIntegerField(default=0)
    finished = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_business_evidence_sources"
        constraints = [
            models.UniqueConstraint(fields=["run", "source_key"], name="ai_business_source_key_uq"),
            models.UniqueConstraint(fields=["run", "ordinal"], name="ai_business_source_ord_uq"),
            models.UniqueConstraint(fields=["run", "domain", "query_digest"], name="ai_business_source_query_uq"),
        ]
        indexes = [models.Index(fields=["run", "finished", "ordinal"], name="ai_business_source_next_idx")]


class AiBusinessSourceToolReceipt(models.Model):
    """One immutable successful owning-tool audit bound to one v3 fact chunk."""
    chunk = models.OneToOneField(AiBusinessEvidenceChunk, primary_key=True,
        on_delete=models.PROTECT, db_column="chunk_id")
    audit = models.OneToOneField("ai_assistant.AiToolAuditLogs", on_delete=models.PROTECT,
        db_column="audit_id")
    run = models.ForeignKey(AiBusinessEvidenceRun, on_delete=models.PROTECT)
    source = models.ForeignKey(AiBusinessEvidenceSource, on_delete=models.PROTECT)
    sequence = models.PositiveIntegerField()
    request_id = models.CharField(max_length=128)
    invocation_id = models.CharField(max_length=160)
    actor_email = models.CharField(max_length=320)
    tool_name = models.CharField(max_length=100)
    surface = models.CharField(max_length=40, default="business_collection")
    response_digest = models.CharField(max_length=64)
    payload_bytes = models.PositiveIntegerField()
    source_ref = models.CharField(max_length=64)
    source_revision = models.CharField(max_length=128)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_business_source_tool_receipts"
        constraints = [models.UniqueConstraint(fields=["run", "source", "sequence"],
            name="ai_business_source_tool_seq_uq")]
        indexes = [models.Index(fields=["run", "source", "sequence"], name="ai_business_tool_receipt_idx")]


class AiBusinessV3ReportIntent(models.Model):
    """Paused, unregistered v3 five-agent intent; never a runnable workflow."""
    id = models.CharField(primary_key=True, max_length=160)
    owner_email = models.CharField(max_length=320)
    scope_json = models.TextField(default="null")
    client_request_id = models.CharField(max_length=160)
    request_digest = models.CharField(max_length=64)
    evidence_run = models.ForeignKey(AiBusinessEvidenceRun, on_delete=models.PROTECT)
    evidence_version = models.PositiveIntegerField()
    sealed_digest = models.CharField(max_length=64)
    candidate_digest = models.CharField(max_length=64)
    snapshot_digest = models.CharField(max_length=64)
    snapshot_json = models.TextField()
    workflow_input_digest = models.CharField(max_length=64)
    workflow_input_json = models.TextField()
    workflow_plan_digest = models.CharField(max_length=64)
    workflow_plan_json = models.TextField()
    status = models.CharField(max_length=20, default="paused")
    pause_reason = models.CharField(max_length=64, default="v3_agents_not_registered")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_business_v3_report_intents"
        constraints = [models.UniqueConstraint(fields=["owner_email", "client_request_id"],
            name="ai_v3_intent_client_uq")]
        indexes = [models.Index(fields=["owner_email", "-created_at"],
            name="ai_v3_intent_owner_idx")]
