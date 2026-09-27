"""AI authority, revision and migration evidence, independent of every business domain."""

import uuid
from django.db import models
from django.utils import timezone


class AiDataRevision(models.Model):
    domain = models.CharField(primary_key=True, max_length=32, default="ai-assistant")
    revision = models.PositiveBigIntegerField(default=0)
    source_digest = models.CharField(max_length=64, default="")
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_data_revisions"


class AiWriteAuthority(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    status = models.CharField(max_length=16, default="d1")
    authority_epoch = models.UUIDField(null=True)
    cutover_id = models.CharField(max_length=128, default="")
    migration_verify_run_id = models.CharField(max_length=64, default="")
    activated_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "ai_write_authority"


class AiWriteReceipt(models.Model):
    request_id = models.CharField(primary_key=True, max_length=128)
    actor_email = models.CharField(max_length=320)
    principal_digest = models.CharField(max_length=64)
    method = models.CharField(max_length=8)
    path = models.CharField(max_length=400)
    query_sha256 = models.CharField(max_length=64)
    body_sha256 = models.CharField(max_length=64)
    status = models.CharField(max_length=20, default="processing")
    response_status = models.PositiveSmallIntegerField(default=200)
    response_payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "ai_write_request_receipts"


class AiMutationAudit(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    request_id = models.CharField(max_length=128, db_index=True)
    actor_email = models.CharField(max_length=320)
    actor_role = models.CharField(max_length=32)
    action = models.CharField(max_length=128)
    scope_digest = models.CharField(max_length=64)
    response_digest = models.CharField(max_length=64)
    revision = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_mutation_audits"


class AiMigrationRun(models.Model):
    id = models.CharField(primary_key=True, max_length=64)
    mode = models.CharField(max_length=16)
    status = models.CharField(max_length=32)
    source_path_digest = models.CharField(max_length=64)
    source_snapshot_digest = models.CharField(max_length=64)
    target_snapshot_digest = models.CharField(max_length=64, default="")
    source_counts = models.JSONField(default=dict)
    target_counts = models.JSONField(default=dict)
    approved_run_id = models.CharField(max_length=64, default="")
    consumed_by_run_id = models.CharField(max_length=64, default="")
    manifest = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "ai_migration_runs"
